import type { TaskStep } from '../../api/types';
import { describeStep } from '../../lib/chats';
import styles from './Thread.module.css';

/** The list of what Deskhand did, one plain line each; a problem or a refusal also shows its note. */
export function StepsList({ steps }: { steps: readonly TaskStep[] }) {
  return (
    <ol className={styles.steps}>
      {steps.map((st, i) => {
        const d = describeStep(st);
        return (
          <li key={st.seq ?? i} data-state={d.state}>
            <span>{d.text}</span>
            {d.note && d.state !== 'ok' ? <span className={styles.note}>{d.note}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

/** A collapsed "N steps" (or "Thinking…" while the task runs) that opens to the list. Nothing at all when there are no steps. */
export function StepsBox({ steps, summary }: { steps: readonly TaskStep[] | undefined; summary: string }) {
  if (!steps?.length) return null;
  return (
    <details className={styles.stepsBox}>
      <summary>{summary}</summary>
      <StepsList steps={steps} />
    </details>
  );
}
